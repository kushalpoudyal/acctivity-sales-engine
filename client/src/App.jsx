import { useState } from 'react'
import './App.css'

const initialTasks = [
  { id: 'idea', label: 'Find a great idea', complete: true },
  { id: 'team', label: 'Bring your team together', complete: true },
  { id: 'build', label: 'Build something that matters', complete: false },
]

function App() {
  const [tasks, setTasks] = useState(initialTasks)
  const completed = tasks.filter((task) => task.complete).length

  function toggleTask(id) {
    setTasks((current) =>
      current.map((task) =>
        task.id === id ? { ...task, complete: !task.complete } : task,
      ),
    )
  }

  return (
    <div className="site-shell">
      <a className="skip-link" href="#main">Skip to content</a>
      <header className="site-header">
        <a className="brand" href="#" aria-label="Hack A home">
          <span className="brand-mark" aria-hidden="true">h.</span>
          hack a<span className="brand-dot">.</span>
        </a>
        <nav aria-label="Main navigation">
          <a href="#features">The possibilities</a>
          <a href="/prospects">Prospect directory</a>
          <a className="nav-cta" href="#playground">Try it out <span aria-hidden="true">&rarr;</span></a>
        </nav>
      </header>

      <main id="main">
        <section className="hero" aria-labelledby="hero-title">
          <div className="hero-copy">
            <p className="eyebrow"><span className="status-dot" /> A little idea. A lot of possibility.</p>
            <h1 id="hero-title">Great things<br />start with<br /><span>a simple idea.</span></h1>
            <p className="hero-description">
              A fresh space to experiment, create, and turn your next
              what-if into something real. Let&apos;s make it happen.
            </p>
            <div className="hero-actions">
              <a className="button button-primary" href="#playground">Let&apos;s get started <span aria-hidden="true">&rarr;</span></a>
              <a className="text-link" href="#features">Take a look around <span aria-hidden="true">&#8600;</span></a>
            </div>
            <p className="hero-note">Made for curious minds. Built with React.</p>
          </div>

          <div className="playground" id="playground">
            <div className="card-topline"><span className="mini-label">YOUR NEXT BIG THING</span><span aria-hidden="true">&bull; &bull; &bull;</span></div>
            <div className="idea-art" aria-hidden="true"><span className="orbit" /><span className="idea-star">&#10035;</span><span className="spark spark-one">+</span><span className="spark spark-two">+</span></div>
            <h2>A little progress, every day.</h2>
            <p className="card-description">Big ideas start with small steps. Try checking one off.</p>
            <div className="task-list">
              {tasks.map((task) => (
                <label className={`task ${task.complete ? 'is-complete' : ''}`} key={task.id}>
                  <input type="checkbox" checked={task.complete} onChange={() => toggleTask(task.id)} />
                  <span>{task.label}</span>
                </label>
              ))}
            </div>
            <div className="progress-heading"><span>Your momentum</span><span role="status">{completed} of {tasks.length} complete</span></div>
            <progress aria-label="Checklist progress" value={completed} max={tasks.length} />
            <p className="card-footnote">{completed === tasks.length ? 'You did it. Now go make it real!' : "You don't have to be ready. Just get started."}</p>
          </div>
        </section>

        <section className="features" id="features" aria-label="The possibilities">
          <article><span className="feature-number">01 / EXPLORE</span><h2>Room to be curious.</h2><p>Follow an idea. Ask a new question. There&apos;s no wrong place to begin.</p></article>
          <article><span className="feature-number">02 / CREATE</span><h2>Less friction. More making.</h2><p>A simple starting point that stays out of the way of your next big thing.</p></article>
          <article><span className="feature-number">03 / GROW</span><h2>Small steps, real progress.</h2><p>Start with something small, keep learning, and see where it takes you.</p></article>
        </section>
      </main>

      <footer><span className="footer-brand">hack a.</span><span>A starting point for something good.</span><span>Go make something.</span></footer>
    </div>
  )
}

export default App
